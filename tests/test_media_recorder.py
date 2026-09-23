from __future__ import annotations

import io
import subprocess
from pathlib import Path

from codex_local_ops import media_ops


class _FailedProcess:
    pid = 1234
    returncode = 1

    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.stderr = io.StringIO("gdigrab failed immediately\n")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def test_recorder_reports_immediate_ffmpeg_failure(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "capture.mp4"
    monkeypatch.setattr(media_ops, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(media_ops.platform, "system", lambda: "Windows")
    monkeypatch.setattr(media_ops, "assert_managed_or_trusted_path", lambda value: Path(value))
    monkeypatch.setattr(media_ops.subprocess, "Popen", lambda *args, **kwargs: _FailedProcess())

    recorder = media_ops.Recorder()
    result = recorder.start(output=str(output))

    assert result["status"] == "FAILED"
    assert result["exit_code"] == 1
    assert "gdigrab failed immediately" in result["reason"]


class _RunningProcess(_FailedProcess):
    returncode = None

    def poll(self):
        return None

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("ffmpeg", timeout)


def test_recorder_start_returns_quickly_when_ffmpeg_stays_running(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "capture.mp4"
    monkeypatch.setattr(media_ops, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(media_ops.platform, "system", lambda: "Windows")
    monkeypatch.setattr(media_ops, "assert_managed_or_trusted_path", lambda value: Path(value))
    monkeypatch.setattr(media_ops.subprocess, "Popen", lambda *args, **kwargs: _RunningProcess())

    recorder = media_ops.Recorder()
    result = recorder.start(output=str(output))

    assert result["status"] == "OK"
    assert result["running"] is True
