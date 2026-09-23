from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import jobs
from .processes import run
from .safety import assert_trusted_path


def backends() -> dict[str, Any]:
    return {
        "remotion": {"available": bool(shutil.which("node") and (shutil.which("npx") or shutil.which("npm"))), "requires_project_dependency": True},
        "manim": {"available": bool(shutil.which("manim")), "path": shutil.which("manim")},
        "blender": {"available": bool(shutil.which("blender")), "path": shutil.which("blender")},
        "html_canvas_webgl": {"available": bool(shutil.which("node")), "render_via": "Playwright + FFmpeg"},
    }


def project_create(path: str, backend: str) -> dict[str, Any]:
    root = assert_trusted_path(path)
    root.mkdir(parents=True, exist_ok=True)
    backend = backend.lower()
    if backend == "remotion":
        package = {
            "name": root.name.lower().replace(" ", "-"), "private": True, "version": "0.1.0",
            "scripts": {"preview": "remotion studio", "render": "remotion render"},
            "dependencies": {"@remotion/cli": "^4.0.0", "@remotion/media": "^4.0.0", "react": "^19.0.0", "react-dom": "^19.0.0", "remotion": "^4.0.0"},
        }
        (root / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
        return {"status": "OK", "path": str(root), "backend": backend, "next": "npm install"}
    if backend == "html_canvas_webgl":
        (root / "index.html").write_text("<!doctype html><meta charset='utf-8'><canvas id='c' width='1280' height='720'></canvas><script>const c=document.querySelector('#c'),x=c.getContext('2d');x.fillStyle='#111';x.fillRect(0,0,c.width,c.height);x.fillStyle='#fff';x.font='48px sans-serif';x.fillText('Codex Local Ops',80,120);</script>\n", encoding="utf-8")
        return {"status": "OK", "path": str(root), "backend": backend}
    if backend == "manim":
        (root / "scene.py").write_text("from manim import *\n\nclass Main(Scene):\n    def construct(self):\n        self.play(Write(Text('Codex Local Ops')))\n", encoding="utf-8")
        return {"status": "OK", "path": str(root), "backend": backend}
    if backend == "blender":
        (root / "render.py").write_text("import bpy\nbpy.context.scene.render.filepath = '//render.png'\nbpy.ops.render.render(write_still=True)\n", encoding="utf-8")
        return {"status": "OK", "path": str(root), "backend": backend}
    raise ValueError(f"Unknown animation backend: {backend}")


def render(path: str, backend: str, *, output: str | None = None, frame: int | None = None) -> dict[str, Any]:
    root = assert_trusted_path(path, must_exist=True)
    backend = backend.lower()
    if backend == "remotion":
        npx = shutil.which("npx")
        if not npx: return {"status": "CAPABILITY_UNAVAILABLE", "reason": "npx not found"}
        args = [npx, "remotion", "still" if frame is not None else "render"]
        if frame is not None: args += ["--frame", str(frame)]
        if output: args += ["--output", str(assert_trusted_path(output))]
        result = run(args, cwd=root, timeout=3600); result["status"] = "OK" if result["exit_code"] == 0 else "FAILED"; return result
    if backend == "manim":
        exe = shutil.which("manim")
        if not exe: return {"status": "CAPABILITY_UNAVAILABLE", "reason": "manim not found"}
        result = run([exe, "-qm", "scene.py", "Main"], cwd=root, timeout=3600); result["status"] = "OK" if result["exit_code"] == 0 else "FAILED"; return result
    if backend == "blender":
        exe = shutil.which("blender")
        if not exe: return {"status": "CAPABILITY_UNAVAILABLE", "reason": "blender not found"}
        args = [exe, "--background", "--python", "render.py"]
        if frame is not None: args += ["--render-frame", str(frame)]
        result = run(args, cwd=root, timeout=3600); result["status"] = "OK" if result["exit_code"] == 0 else "FAILED"; return result
    return {"status": "CAPABILITY_UNAVAILABLE", "reason": f"Direct render for backend {backend} is not available"}


def render_async(path: str, backend: str, *, output: str | None = None, frame: int | None = None) -> dict[str, Any]:
    root = assert_trusted_path(path, must_exist=True)
    backend = backend.lower()
    args: list[str]
    if backend == "remotion":
        npx = shutil.which("npx")
        if not npx:
            return {"status": "CAPABILITY_UNAVAILABLE", "reason": "npx not found"}
        args = [npx, "remotion", "still" if frame is not None else "render"]
        if frame is not None:
            args += ["--frame", str(frame)]
        if output:
            args += ["--output", str(assert_trusted_path(output))]
    elif backend == "manim":
        exe = shutil.which("manim")
        if not exe:
            return {"status": "CAPABILITY_UNAVAILABLE", "reason": "manim not found"}
        args = [exe, "-qm", "scene.py", "Main"]
    elif backend == "blender":
        exe = shutil.which("blender")
        if not exe:
            return {"status": "CAPABILITY_UNAVAILABLE", "reason": "blender not found"}
        args = [exe, "--background", "--python", "render.py"]
        if frame is not None:
            args += ["--render-frame", str(frame)]
    else:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": f"Direct render for backend {backend} is not available"}

    result = jobs.start(args, cwd=root, label=f"animation-{backend}")
    result["backend"] = backend
    if output:
        result["output"] = str(assert_trusted_path(output))
    return result
