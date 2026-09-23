from __future__ import annotations

from collections import deque
import json
import math
import platform
import shutil
import subprocess
import threading
import time
import os
from pathlib import Path
from typing import Any

from . import jobs
from .config import install_root
from .models import unavailable
from .processes import run
from .safety import assert_managed_or_trusted_path


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _ffprobe() -> str | None:
    return shutil.which("ffprobe")


def video_info(path: str) -> dict[str, Any]:
    p = assert_managed_or_trusted_path(path, must_exist=True)
    exe = _ffprobe()
    if not exe:
        return unavailable("ffprobe executable not found", platform.system().lower())
    return run([exe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(p)])


def _convert_argv(
    inputs: list[str],
    output: str,
    filters: list[str] | None = None,
    extra: list[str] | None = None,
) -> tuple[list[str], Path] | None:
    exe = _ffmpeg()
    if not exe:
        return None
    argv = [exe, "-y"]
    for item in inputs:
        p = assert_managed_or_trusted_path(item, must_exist=True)
        argv += ["-i", str(p)]
    out = assert_managed_or_trusted_path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if filters:
        argv += ["-vf", ",".join(filters)]
    if extra:
        argv += extra
    argv.append(str(out))
    return argv, out


def _convert(inputs: list[str], output: str, filters: list[str] | None = None, extra: list[str] | None = None, timeout: int = 1800) -> dict:
    built = _convert_argv(inputs, output, filters=filters, extra=extra)
    if built is None:
        return unavailable("ffmpeg executable not found", platform.system().lower())
    argv, out = built
    result = run(argv, timeout=timeout)
    result["status"] = "OK" if result["exit_code"] == 0 and out.exists() else "FAILED"
    result["output"] = str(out)
    return result


def _convert_async(
    inputs: list[str],
    output: str,
    *,
    filters: list[str] | None = None,
    extra: list[str] | None = None,
    label: str,
) -> dict[str, Any]:
    built = _convert_argv(inputs, output, filters=filters, extra=extra)
    if built is None:
        return unavailable("ffmpeg executable not found", platform.system().lower())
    argv, out = built
    result = jobs.start(argv, label=label)
    result["output"] = str(out)
    return result


def convert(src: str, output: str) -> dict: return _convert([src], output)
def trim(src: str, output: str, start: float, duration: float) -> dict: return _convert([src], output, extra=["-ss", str(start), "-t", str(duration), "-c", "copy"])
def resize(src: str, output: str, width: int, height: int) -> dict: return _convert([src], output, filters=[f"scale={width}:{height}"])
def change_fps(src: str, output: str, fps: float) -> dict: return _convert([src], output, filters=[f"fps={fps}"])
def extract_frame(src: str, output: str, timestamp: float) -> dict: return _convert([src], output, extra=["-ss", str(timestamp), "-frames:v", "1"])
def thumbnail(src: str, output: str, timestamp: float = 0.0) -> dict: return extract_frame(src, output, timestamp)
def remove_audio(src: str, output: str) -> dict: return _convert([src], output, extra=["-an", "-c:v", "copy"])
def extract_audio(src: str, output: str) -> dict: return _convert([src], output, extra=["-vn"])
def to_gif(src: str, output: str, fps: float = 12, width: int = 720) -> dict: return _convert([src], output, filters=[f"fps={fps}", f"scale={width}:-1:flags=lanczos"])
def gif_to_video(src: str, output: str) -> dict: return _convert([src], output, extra=["-pix_fmt", "yuv420p", "-movflags", "+faststart"])


def convert_async(src: str, output: str) -> dict:
    return _convert_async([src], output, label="video-convert")


def trim_async(src: str, output: str, start: float, duration: float) -> dict:
    return _convert_async([src], output, extra=["-ss", str(start), "-t", str(duration), "-c", "copy"], label="video-trim")


def resize_async(src: str, output: str, width: int, height: int) -> dict:
    return _convert_async([src], output, filters=[f"scale={width}:{height}"], label="video-resize")


def change_fps_async(src: str, output: str, fps: float) -> dict:
    return _convert_async([src], output, filters=[f"fps={fps}"], label="video-fps")


def extract_frame_async(src: str, output: str, timestamp: float) -> dict:
    return _convert_async([src], output, extra=["-ss", str(timestamp), "-frames:v", "1"], label="video-frame")


def remove_audio_async(src: str, output: str) -> dict:
    return _convert_async([src], output, extra=["-an", "-c:v", "copy"], label="video-remove-audio")


def extract_audio_async(src: str, output: str) -> dict:
    return _convert_async([src], output, extra=["-vn"], label="video-extract-audio")


def to_gif_async(src: str, output: str, fps: float = 12, width: int = 720) -> dict:
    return _convert_async([src], output, filters=[f"fps={fps}", f"scale={width}:-1:flags=lanczos"], label="video-to-gif")


def gif_to_video_async(src: str, output: str) -> dict:
    return _convert_async([src], output, extra=["-pix_fmt", "yuv420p", "-movflags", "+faststart"], label="gif-to-video")


def concat(paths: list[str], output: str) -> dict:
    if not paths:
        raise ValueError("At least one input is required")
    inputs = [assert_managed_or_trusted_path(x, must_exist=True) for x in paths]
    list_file = install_root() / "tmp" / f"concat-{int(time.time()*1000)}.txt"
    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text("\n".join("file '" + str(p).replace("'", "'\\''") + "'" for p in inputs), encoding="utf-8")
    out = assert_managed_or_trusted_path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    result = run([_ffmpeg() or "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)], timeout=1800)
    list_file.unlink(missing_ok=True)
    result["status"] = "OK" if result["exit_code"] == 0 and out.exists() else "FAILED"
    return result


def concat_async(paths: list[str], output: str) -> dict[str, Any]:
    if not paths:
        raise ValueError("At least one input is required")
    exe = _ffmpeg()
    if not exe:
        return unavailable("ffmpeg executable not found", platform.system().lower())
    inputs = [assert_managed_or_trusted_path(x, must_exist=True) for x in paths]
    list_file = install_root() / "tmp" / f"concat-{int(time.time()*1000)}.txt"
    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text("\n".join("file '" + str(p).replace("'", "'\\''") + "'" for p in inputs), encoding="utf-8")
    out = assert_managed_or_trusted_path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    result = jobs.start(
        [exe, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)],
        label="video-concat",
        cleanup_paths=[list_file],
    )
    result["output"] = str(out)
    return result


def add_audio(video: str, audio: str, output: str, replace: bool = False) -> dict:
    extra = ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-shortest"]
    return _convert([video, audio], output, extra=extra)


def add_audio_async(video: str, audio: str, output: str) -> dict:
    extra = ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-shortest"]
    return _convert_async([video, audio], output, extra=extra, label="video-add-audio")


def add_subtitles(video: str, subtitles: str, output: str) -> dict:
    sub = assert_managed_or_trusted_path(subtitles, must_exist=True)
    escaped = str(sub).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return _convert([video], output, filters=[f"subtitles='{escaped}'"])


def add_subtitles_async(video: str, subtitles: str, output: str) -> dict:
    sub = assert_managed_or_trusted_path(subtitles, must_exist=True)
    escaped = str(sub).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return _convert_async([video], output, filters=[f"subtitles='{escaped}'"], label="video-add-subtitles")


def add_text(video: str, output: str, text: str, x: str = "(w-text_w)/2", y: str = "h-text_h-40") -> dict:
    safe = text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    return _convert([video], output, filters=[f"drawtext=text='{safe}':x={x}:y={y}"])


def overlay_image(video: str, image: str, output: str, x: int = 0, y: int = 0) -> dict:
    return _convert([video, image], output, extra=["-filter_complex", f"[0:v][1:v]overlay={x}:{y}"])


def extract_frames(video: str, output_pattern: str, fps: float = 1.0) -> dict:
    exe = _ffmpeg()
    if not exe:
        return unavailable("ffmpeg executable not found", platform.system().lower(), feature="extract_frames")
    src = assert_managed_or_trusted_path(video, must_exist=True)
    pattern = assert_managed_or_trusted_path(output_pattern)
    pattern.parent.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in pattern.parent.iterdir() if p.is_file()}
    result = run([exe, "-y", "-i", str(src), "-vf", f"fps={fps}", str(pattern)], timeout=1800)
    after = [str(p) for p in pattern.parent.iterdir() if p.is_file() and p.name not in before]
    result["status"] = "OK" if result.get("exit_code") == 0 and after else "FAILED"
    result["output_pattern"] = str(pattern)
    result["files"] = sorted(after)
    return result


class Recorder:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.output: Path | None = None
        self.kind: str | None = None
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._stderr_thread: threading.Thread | None = None
        self._last_exit_code: int | None = None

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        if proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                self._stderr_tail.append(str(line).rstrip())
        except Exception:
            return

    def _failure_reason(self, exit_code: int | None) -> str:
        detail = "\n".join(self._stderr_tail).strip()
        prefix = f"FFmpeg recording exited unexpectedly with code {exit_code}"
        return f"{prefix}: {detail[-4000:]}" if detail else prefix

    def status(self) -> dict[str, Any]:
        running = bool(self.proc and self.proc.poll() is None)
        exit_code = self.proc.poll() if self.proc else self._last_exit_code
        return {
            "running": running,
            "pid": self.proc.pid if running else None,
            "output": str(self.output) if self.output else None,
            "kind": self.kind,
            "exit_code": exit_code,
        }

    def start(self, kind: str = "screen", *, fps: int = 15, output: str | None = None, monitor: int = 0, window: str | None = None, region: str | None = None, audio: bool = False) -> dict[str, Any]:
        if self.proc and self.proc.poll() is None:
            return {"status": "FAILED", "reason": "A recording is already active", **self.status()}
        ffmpeg = _ffmpeg()
        if not ffmpeg:
            return unavailable("ffmpeg executable not found", platform.system().lower())
        out = assert_managed_or_trusted_path(output) if output else install_root() / "videos" / f"{kind}-{int(time.time()*1000)}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        system = platform.system()
        args = [ffmpeg, "-y"]
        if system == "Windows":
            args += ["-f", "gdigrab", "-framerate", str(fps)]
            if region:
                x, y, w, h = [int(v) for v in region.split(",")]
                args += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}", "-i", "desktop"]
            elif kind == "window" and window:
                args += ["-i", f"title={window}"]
            else:
                args += ["-i", "desktop"]
        elif system == "Linux":
            session = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
            if session == "wayland":
                return unavailable("Wayland recording requires PipeWire/portal session negotiation", "linux", "Use xdg-desktop-portal/PipeWire or OBS configured for Wayland capture")
            display = os.environ.get("DISPLAY")
            if not display:
                return unavailable("No X11 DISPLAY is available", "linux")
            args += ["-f", "x11grab", "-framerate", str(fps)]
            if region:
                x, y, w, h = [int(v) for v in region.split(",")]
                args += ["-video_size", f"{w}x{h}", "-i", f"{display}+{x},{y}"]
            else:
                args += ["-i", display]
        elif system == "Darwin":
            return unavailable("Generic macOS capture needs a confirmed ScreenCaptureKit/AVFoundation capture source", "macos", "Grant Screen Recording permission and configure OBS or an AVFoundation capture device")
        else:
            return unavailable("Screen recording backend unavailable", system.lower())
        if audio:
            return unavailable("Desktop audio capture requires an explicitly configured platform audio source", system.lower(), "Configure OBS or a platform audio capture device; microphone stays OFF by default")
        args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._stderr_tail.clear()
        self._last_exit_code = None
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, creationflags=flags)
        self.output, self.kind = out, kind
        self._stderr_thread = threading.Thread(target=self._drain_stderr, args=(self.proc,), daemon=True)
        self._stderr_thread.start()
        try:
            exit_code = self.proc.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            return {"status": "OK", **self.status()}
        self._last_exit_code = exit_code
        self._stderr_thread.join(timeout=0.1)
        return {"status": "FAILED", "reason": self._failure_reason(exit_code), **self.status()}

    def stop(self) -> dict[str, Any]:
        if not self.proc:
            return {"status": "OK", **self.status()}
        if self.proc.poll() is not None:
            self._last_exit_code = self.proc.returncode
            if self._stderr_thread:
                self._stderr_thread.join(timeout=0.1)
            ok = self.proc.returncode == 0 and bool(self.output and self.output.exists())
            result = {"status": "OK" if ok else "FAILED", **self.status()}
            if not ok:
                result["reason"] = self._failure_reason(self.proc.returncode)
            return result
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write("q\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        self._last_exit_code = self.proc.returncode
        if self._stderr_thread:
            self._stderr_thread.join(timeout=0.1)
        ok = self.proc.returncode == 0 and bool(self.output and self.output.exists())
        result = {"status": "OK" if ok else "FAILED", **self.status()}
        result["output"] = str(self.output) if self.output else None
        if not ok:
            result["reason"] = self._failure_reason(self.proc.returncode)
        return result


recorder = Recorder()


def sample_frames(video: str, count: int = 6) -> dict[str, Any]:
    src = assert_managed_or_trusted_path(video, must_exist=True)
    info = video_info(str(src))
    duration = None
    try:
        payload = json.loads(info.get("stdout", "{}"))
        duration = float(payload.get("format", {}).get("duration", 0))
    except Exception:
        duration = 0
    if not duration:
        return {"status": "FAILED", "reason": "Could not determine video duration", "probe": info}
    out_dir = install_root() / "frames" / f"sample-{int(time.time()*1000)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(max(1, count)):
        t = duration * (i + 0.5) / count
        p = out_dir / f"frame-{i+1:03}.png"
        res = extract_frame(str(src), str(p), t)
        if res.get("status") == "OK": files.append(str(p))
    return {"status": "OK" if files else "FAILED", "duration": duration, "frames": files}


def contact_sheet(video: str, output: str, count: int = 6, columns: int = 3, width: int = 320) -> dict[str, Any]:
    src = assert_managed_or_trusted_path(video, must_exist=True)
    if count < 1 or columns < 1 or width < 32:
        raise ValueError("count and columns must be positive; width must be at least 32")
    info = video_info(str(src))
    try:
        payload = json.loads(info.get("stdout", "{}"))
        duration = float(payload.get("format", {}).get("duration", 0))
    except Exception:
        duration = 0.0
    if duration <= 0:
        return {"status": "FAILED", "reason": "Could not determine video duration", "probe": info}
    rows = max(1, math.ceil(count / columns))
    fps = max(0.000001, count / duration)
    filters = [
        f"fps={fps:.8f}",
        f"scale={width}:-1",
        f"tile={columns}x{rows}:nb_frames={count}:padding=8:margin=8",
    ]
    result = _convert([str(src)], output, filters=filters, extra=["-frames:v", "1"])
    result["duration"] = duration
    result["count"] = count
    result["columns"] = columns
    return result
