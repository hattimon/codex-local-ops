from __future__ import annotations

from pathlib import Path

from codex_local_ops import gui


def test_gui_default_root_prefers_configured_trusted_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gui, "first_run_status", lambda: {"trusted_roots": [str(tmp_path)]})

    assert gui._default_root() == str(tmp_path)


def test_gui_default_root_falls_back_to_current_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gui, "first_run_status", lambda: {"trusted_roots": []})
    monkeypatch.chdir(tmp_path)

    assert gui._default_root() == str(tmp_path.resolve())
