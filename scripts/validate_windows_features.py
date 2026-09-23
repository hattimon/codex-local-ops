"""Short functional smoke for optional Windows Local Ops capabilities.

This validator intentionally keeps every operation small. Long FFmpeg work is
started through the persisted async job manager and polled in short intervals.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from codex_local_ops import animation_ops, desktop_ops, gui, jobs, media_ops, obs_ops, wizard
from codex_local_ops.browser_ops import BrowserManager
from codex_local_ops.config import install_root


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs" / "validation" / "windows-feature-smoke-2026-09-23.json"


def _capture(fn) -> dict[str, Any]:
    try:
        value = fn()
        return value if isinstance(value, dict) else {"status": "OK", "value": value}
    except Exception as exc:
        return {"status": "FAILED", "reason": f"{type(exc).__name__}: {exc}"}


def _browser_smoke() -> dict[str, Any]:
    manager = BrowserManager()
    out: dict[str, Any] = {"before": manager.status()}
    fixture = install_root() / "validation-browser.html"
    screenshot = install_root() / "screenshots" / "validation-browser.png"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(
        "<!doctype html><title>Local Ops Smoke</title>"
        "<main><h1>Codex Local Ops</h1><p id='ready'>browser-ready</p></main>",
        encoding="utf-8",
    )
    started = manager.start(headless=True)
    out["start"] = started
    if started.get("status") != "OK":
        return out
    try:
        out["navigate"] = manager.navigate(fixture.as_uri(), timeout_ms=10_000)
        out["snapshot"] = manager.snapshot()
        out["find"] = manager.find("browser-ready")
        out["screenshot"] = manager.screenshot(str(screenshot))
    finally:
        out["stop"] = manager.stop()
    return out


def _desktop_smoke() -> dict[str, Any]:
    screenshot = install_root() / "screenshots" / "validation-desktop.png"
    return {
        "info": _capture(desktop_ops.info),
        "windows": _capture(desktop_ops.list_windows),
        "screenshot": _capture(lambda: desktop_ops.screenshot(str(screenshot))),
    }


def _gui_smoke() -> dict[str, Any]:
    out: dict[str, Any] = {
        "first_run": _capture(wizard.first_run_status),
        "default_root": _capture(lambda: {"path": gui._default_root()}),
    }
    try:
        import tkinter as tk

        out["tkinter"] = {"status": "OK", "tk_version": str(tk.TkVersion)}
    except Exception as exc:
        out["tkinter"] = {"status": "CAPABILITY_UNAVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}
    return out


def _media_smoke() -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    out: dict[str, Any] = {"ffmpeg": ffmpeg, "ffprobe": ffprobe, "recorder_before": media_ops.recorder.status()}
    if not ffmpeg:
        out["status"] = "CAPABILITY_UNAVAILABLE"
        out["reason"] = "ffmpeg not found"
        return out

    media_dir = install_root() / "validation-media"
    media_dir.mkdir(parents=True, exist_ok=True)
    source = media_dir / "source.mp4"
    frame = media_dir / "frame.png"
    create = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:d=1",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    out["fixture"] = {"exit_code": create.returncode, "exists": source.exists()}
    if create.returncode != 0 or not source.exists():
        out["status"] = "FAILED"
        out["reason"] = create.stderr[-2000:]
        return out

    out["video_info"] = media_ops.video_info(str(source))
    started = media_ops.extract_frame_async(str(source), str(frame), 0.25)
    out["async_start"] = started
    session_id = started.get("session_id")
    if session_id:
        deadline = time.monotonic() + 15
        state = jobs.status(str(session_id))
        while state.get("status") not in jobs.TERMINAL_STATES and time.monotonic() < deadline:
            time.sleep(0.1)
            state = jobs.status(str(session_id))
        out["async_status"] = state
        out["async_output"] = jobs.output(str(session_id), max_bytes=20_000)
        out["frame_exists"] = frame.exists()

    recording = media_ops.recorder.start("screen", fps=5, output=str(media_dir / "screen-smoke.mp4"))
    out["recording_start"] = recording
    if recording.get("status") == "OK":
        try:
            time.sleep(0.8)
            out["recording_running"] = media_ops.recorder.status()
        finally:
            out["recording_stop"] = media_ops.recorder.stop()
    return out


def main() -> None:
    stage = sys.argv[1].lower() if len(sys.argv) > 1 else "passive"
    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "local_ops_home": str(install_root()),
        "stage": stage,
    }
    if stage == "passive":
        manager = BrowserManager()
        report.update(
            {
                "browser_status": _capture(manager.status),
                "desktop_info": _capture(desktop_ops.info),
                "recorder_status": _capture(media_ops.recorder.status),
                "ffmpeg": shutil.which("ffmpeg"),
                "ffprobe": shutil.which("ffprobe"),
                "animation_backends": _capture(animation_ops.backends),
                "first_run": _capture(wizard.first_run_status),
            }
        )
    elif stage == "browser":
        report["browser"] = _capture(_browser_smoke)
    elif stage == "desktop":
        report["desktop"] = _capture(_desktop_smoke)
    elif stage == "gui":
        report["gui"] = _capture(_gui_smoke)
    elif stage == "media":
        report["media"] = _capture(_media_smoke)
    elif stage == "obs":
        report["obs_status"] = _capture(lambda: obs_ops.call("status"))
    else:
        raise SystemExit("stage must be one of: passive, browser, desktop, gui, media, obs")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if REPORT_PATH.exists():
        try:
            existing = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing.setdefault("runs", []).append(report)
    REPORT_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "OK", "report": str(REPORT_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
