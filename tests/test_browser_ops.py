from __future__ import annotations

from types import SimpleNamespace

from codex_local_ops import browser_ops
from codex_local_ops.browser_ops import BrowserManager


class _Locator:
    def __init__(self, text: str = "", count: int = 0) -> None:
        self._text = text
        self._count = count

    def inner_text(self) -> str:
        return self._text

    def count(self) -> int:
        return self._count


class _Page:
    url = "https://example.test/"

    def title(self) -> str:
        return "Example"

    def locator(self, selector: str) -> _Locator:
        return _Locator("page body")

    def get_by_text(self, text: str, exact: bool = False) -> _Locator:
        return _Locator(count=2)


def test_browser_read_apis_return_explicit_ok_status() -> None:
    manager = BrowserManager()
    page = _Page()
    manager._context = SimpleNamespace(pages=[page])
    manager._page = page

    snapshot = manager.snapshot()
    text = manager.get_text("body")
    found = manager.find("body")

    assert snapshot["status"] == "OK"
    assert snapshot["title"] == "Example"
    assert text == {"status": "OK", "selector": "body", "text": "page body"}
    assert found == {"status": "OK", "text": "body", "count": 2}


def test_browser_rejects_unknown_url_scheme() -> None:
    manager = BrowserManager()
    try:
        manager._check_url("javascript:alert(1)")
    except PermissionError as exc:
        assert "scheme" in str(exc).lower()
    else:
        raise AssertionError("javascript: URL should have been rejected")


def test_browser_system_executable_falls_back_to_path(monkeypatch) -> None:
    monkeypatch.setattr(browser_ops.shutil, "which", lambda name: "C:/browser/chrome.exe" if name == "chrome.exe" else None)

    assert browser_ops._find_system_browser_executable() == "C:/browser/chrome.exe"


def test_browser_inaccessible_candidate_is_treated_as_missing() -> None:
    class _InaccessiblePath:
        def is_file(self) -> bool:
            raise PermissionError("blocked by host")

    assert browser_ops._path_is_file(_InaccessiblePath()) is False
