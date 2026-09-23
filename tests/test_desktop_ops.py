from __future__ import annotations

from pathlib import Path

from codex_local_ops import desktop_ops


def test_wayland_global_input_reports_capability_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(desktop_ops, "require_mode", lambda *modes: None)
    monkeypatch.setattr(desktop_ops.platform, "system", lambda: "Linux")
    monkeypatch.setattr(desktop_ops, "info", lambda: {"session": "wayland"})

    result = desktop_ops.input_action("click", x=10, y=20)

    assert result["status"] == "CAPABILITY_UNAVAILABLE"
    assert "Wayland" in result["reason"]


def test_desktop_screenshot_delegates_to_platform_backend(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "shot.png"

    class Backend:
        def take_screenshot(self, path: Path):
            return {"status": "OK", "path": str(path), "width": 10, "height": 20}

    monkeypatch.setattr(desktop_ops, "assert_managed_or_trusted_path", lambda path: Path(path))
    monkeypatch.setattr(desktop_ops, "current_backend", lambda: Backend())

    result = desktop_ops.screenshot(str(output))

    assert result["status"] == "OK"
    assert result["path"] == str(output)
