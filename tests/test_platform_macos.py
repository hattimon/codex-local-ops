from __future__ import annotations

import json

from codex_local_ops.platforms import macos
from codex_local_ops.platforms.macos import MacOSBackend


def test_macos_list_windows_returns_structured_rows(monkeypatch) -> None:
    rows = [{"handle": "123:1", "pid": 123, "process": "Editor", "title": "Project", "visible": True}]
    monkeypatch.setattr(macos.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None)
    monkeypatch.setattr(macos, "run", lambda argv: {"exit_code": 0, "stdout": json.dumps(rows), "stderr": ""})
    result = MacOSBackend().list_windows()
    assert result == {"status": "OK", "windows": rows}


def test_macos_window_action_rejects_non_inventory_handle(monkeypatch) -> None:
    monkeypatch.setattr(macos.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None)
    result = MacOSBackend().window_action("minimize", "Editor")
    assert result["status"] == "FAILED"
    assert "pid:index" in result["reason"]


def test_macos_focus_window_uses_pid_index_and_axraise(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(macos.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None)

    def fake_run(argv: list[str]) -> dict[str, object]:
        calls.append(argv)
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(macos, "run", fake_run)
    result = MacOSBackend().focus_window("123:2")
    assert result["status"] == "OK"
    assert result["handle"] == "123:2"
    script = calls[0][-1]
    assert "unix id is 123" in script
    assert 'perform action "AXRaise" of window 2' in script


def test_macos_window_action_minimize_uses_inventory_handle(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(macos.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None)

    def fake_run(argv: list[str]) -> dict[str, object]:
        calls.append(argv)
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(macos, "run", fake_run)
    result = MacOSBackend().window_action("minimize", "456:3")
    assert result["status"] == "OK"
    assert result["action"] == "minimize"
    assert result["handle"] == "456:3"
    script = calls[0][-1]
    assert "unix id is 456" in script
    assert "window 3 of targetProcess" in script
    assert 'attribute "AXMinimized" of targetWindow to true' in script


def test_macos_accessibility_failure_has_setup_guidance(monkeypatch) -> None:
    monkeypatch.setattr(macos.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None)
    monkeypatch.setattr(macos, "run", lambda argv: {"exit_code": 1, "stdout": "", "stderr": "Not authorized"})
    result = MacOSBackend().focus_window("123:1")
    assert result["status"] == "FAILED"
    assert "Accessibility" in result["suggested_setup"]
