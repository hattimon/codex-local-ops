from __future__ import annotations

from codex_local_ops.platforms import linux
from codex_local_ops.platforms.linux import LinuxBackend


def test_linux_wayland_window_action_reports_capability_limit(monkeypatch) -> None:
    backend = LinuxBackend()
    monkeypatch.setattr(backend, "get_desktop_info", lambda: {"available": True, "session": "wayland"})

    result = backend.window_action("close", "0x123")

    assert result["status"] == "CAPABILITY_UNAVAILABLE"
    assert "Wayland" in result["reason"]


def test_linux_x11_window_inventory_parses_wmctrl(monkeypatch) -> None:
    backend = LinuxBackend()
    monkeypatch.setattr(backend, "get_desktop_info", lambda: {"available": True, "session": "x11"})
    monkeypatch.setattr(linux.shutil, "which", lambda name: "/usr/bin/wmctrl" if name == "wmctrl" else None)
    monkeypatch.setattr(
        linux,
        "run",
        lambda argv: {
            "exit_code": 0,
            "stdout": "0x01200003  0 app.Editor host Project Window\n",
            "stderr": "",
        },
    )

    result = backend.list_windows()

    assert result["status"] == "OK"
    assert result["windows"] == [
        {
            "handle": "0x01200003",
            "desktop": "0",
            "class": "app.Editor",
            "host": "host",
            "title": "Project Window",
            "visible": True,
        }
    ]


def test_linux_x11_maximize_uses_wmctrl(monkeypatch) -> None:
    backend = LinuxBackend()
    calls: list[list[str]] = []
    monkeypatch.setattr(backend, "get_desktop_info", lambda: {"available": True, "session": "x11"})
    monkeypatch.setattr(linux.shutil, "which", lambda name: "/usr/bin/wmctrl" if name == "wmctrl" else None)

    def fake_run(argv: list[str]) -> dict[str, object]:
        calls.append(argv)
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(linux, "run", fake_run)
    result = backend.window_action("maximize", "0x123")

    assert result["status"] == "OK"
    assert calls == [["/usr/bin/wmctrl", "-ir", "0x123", "-b", "add,maximized_vert,maximized_horz"]]
