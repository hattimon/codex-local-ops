from __future__ import annotations

from pathlib import Path

from codex_local_ops import media_ops


def _allow_paths(monkeypatch) -> None:
    monkeypatch.setattr(media_ops, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(
        media_ops,
        "assert_managed_or_trusted_path",
        lambda path, must_exist=False: Path(path),
    )


def test_video_convert_async_delegates_to_job_manager(tmp_path: Path, monkeypatch) -> None:
    _allow_paths(monkeypatch)
    captured: dict = {}

    def fake_start(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return {"status": "STARTED", "session_id": "job_deadbeef"}

    monkeypatch.setattr(media_ops.jobs, "start", fake_start)
    monkeypatch.setattr(media_ops, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sync runner used")))

    output = tmp_path / "out.mp4"
    result = media_ops.convert_async("input.mp4", str(output))

    assert result["status"] == "STARTED"
    assert result["session_id"] == "job_deadbeef"
    assert result["output"] == str(output)
    assert captured["kwargs"]["label"] == "video-convert"
    assert captured["argv"][0] == "ffmpeg"


def test_video_concat_async_registers_temp_list_for_cleanup(tmp_path: Path, monkeypatch) -> None:
    _allow_paths(monkeypatch)
    monkeypatch.setattr(media_ops, "install_root", lambda: tmp_path / "home")
    captured: dict = {}

    def fake_start(argv, **kwargs):
        captured["kwargs"] = kwargs
        return {"status": "STARTED", "session_id": "job_concat"}

    monkeypatch.setattr(media_ops.jobs, "start", fake_start)
    result = media_ops.concat_async(["one.mp4", "two.mp4"], str(tmp_path / "joined.mp4"))

    cleanup_paths = captured["kwargs"]["cleanup_paths"]
    assert result["status"] == "STARTED"
    assert len(cleanup_paths) == 1
    assert Path(cleanup_paths[0]).exists()
