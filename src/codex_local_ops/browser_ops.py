from __future__ import annotations

import json
import os
import platform
import shutil
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import install_root, load_config
from .models import unavailable
from .safety import assert_managed_or_trusted_path, assert_trusted_path


def _path_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _find_system_browser_executable() -> str | None:
    names = ["chromium", "chromium-browser", "google-chrome", "chrome", "msedge"]
    if os.name == "nt":
        names = ["chrome.exe", "msedge.exe", "chromium.exe", *names]
    for name in names:
        executable = shutil.which(name)
        if executable:
            return executable

    if platform.system() != "Windows":
        return None

    candidates: list[Path] = []
    for root_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(root_name)
        if not root:
            continue
        base = Path(root)
        candidates.extend(
            [
                base / "Google" / "Chrome" / "Application" / "chrome.exe",
                base / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                base / "Chromium" / "Application" / "chrome.exe",
            ]
        )
    return next((str(path) for path in candidates if _path_is_file(path)), None)


class BrowserManager:
    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = threading.RLock()
        self._recording = False
        self._last_video: str | None = None
        self._console_messages: list[dict[str, str]] = []
        self._network_errors: list[dict[str, str]] = []

    @property
    def profile_dir(self) -> Path:
        path = install_root() / "browser-profile"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def downloads_dir(self) -> Path:
        path = install_root() / "downloads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def status(self) -> dict[str, Any]:
        cfg = load_config().get("browser", {})
        try:
            import playwright  # noqa: F401
            package = True
        except Exception:
            package = False
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "package": package,
            "running": self._context is not None,
            "pages": len(self._context.pages) if self._context else 0,
            "profile": str(self.profile_dir),
            "headless": bool(cfg.get("headless", False)),
            "channel": str(cfg.get("channel", "chromium")),
            "recording": self._recording,
            "last_video": self._last_video,
        }

    def _wire_page(self, page) -> None:
        page.on("console", lambda msg: self._console_messages.append({"type": msg.type, "text": msg.text, "url": page.url}))
        page.on("requestfailed", lambda req: self._network_errors.append({"url": req.url, "failure": str(req.failure)}))

    def start(self, *, headless: bool | None = None, record_video: bool = False) -> dict[str, Any]:
        with self._lock:
            cfg = load_config().get("browser", {})
            if not cfg.get("enabled", True):
                return {"status": "PERMISSION_DENIED", "reason": "Browser automation is disabled in config"}
            if self._context:
                return {"status": "OK", **self.status()}
            try:
                from playwright.sync_api import sync_playwright
            except Exception as exc:
                return unavailable(f"Playwright is unavailable: {exc}", "browser", "Install the project dependencies and Playwright Chromium")
            self._pw = sync_playwright().start()
            use_headless = bool(cfg.get("headless", False)) if headless is None else headless
            kwargs: dict[str, Any] = {
                "user_data_dir": str(self.profile_dir),
                "headless": use_headless,
                "accept_downloads": True,
            }
            executable = cfg.get("executable")
            if executable:
                kwargs["executable_path"] = str(executable)
            channel = str(cfg.get("channel", "chromium") or "chromium").lower()
            if channel != "chromium":
                kwargs["channel"] = channel
            elif not executable:
                try:
                    bundled = Path(self._pw.chromium.executable_path)
                except Exception:
                    bundled = None
                if not bundled or not _path_is_file(bundled):
                    system_browser = _find_system_browser_executable()
                    if system_browser:
                        kwargs["executable_path"] = system_browser
            if record_video:
                video_dir = install_root() / "videos" / "browser"
                video_dir.mkdir(parents=True, exist_ok=True)
                kwargs["record_video_dir"] = str(video_dir)
            try:
                self._context = self._pw.chromium.launch_persistent_context(**kwargs)
                self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
                self._console_messages.clear()
                self._network_errors.clear()
                for page in self._context.pages:
                    self._wire_page(page)
                self._context.on("page", self._wire_page)
                self._recording = record_video
                return {"status": "OK", **self.status()}
            except Exception as exc:
                if self._pw:
                    self._pw.stop()
                self._pw = None
                self._context = None
                self._page = None
                return unavailable(
                    f"Browser could not start: {exc}",
                    "browser",
                    "Run: playwright install chromium, or configure browser.executable",
                )

    def stop(self) -> dict[str, Any]:
        with self._lock:
            video = None
            if self._page and self._page.video:
                try:
                    video = self._page.video.path()
                except Exception:
                    video = None
            if self._context:
                self._context.close()
            if self._pw:
                self._pw.stop()
            self._context = self._page = self._pw = None
            self._recording = False
            if video:
                self._last_video = str(video)
            return {"status": "OK", "last_video": self._last_video}

    def _ensure(self):
        if not self._context:
            result = self.start()
            if result.get("status") != "OK":
                raise RuntimeError(result.get("reason", "Browser unavailable"))
        if self._page is None:
            self._page = self._context.new_page()
        return self._page

    def _check_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https", "file", "about"}:
            raise PermissionError(f"URL scheme is not allowed: {parsed.scheme}")
        excluded = [str(x).lower() for x in load_config().get("privacy", {}).get("excluded_domains", [])]
        host = (parsed.hostname or "").lower()
        if any(host == d or host.endswith("." + d) for d in excluded):
            raise PermissionError("Domain is excluded by privacy settings")

    def tabs(self) -> dict[str, Any]:
        self._ensure()
        return {"tabs": [{"index": i, "url": p.url, "title": p.title()} for i, p in enumerate(self._context.pages)]}

    def open(self, url: str | None = None) -> dict[str, Any]:
        self._ensure()
        page = self._context.new_page()
        self._page = page
        if url:
            self.navigate(url)
        return {"status": "OK", "index": self._context.pages.index(page), "url": page.url}

    def close(self, index: int | None = None) -> dict[str, Any]:
        self._ensure()
        page = self._context.pages[index] if index is not None else self._page
        page.close()
        self._page = self._context.pages[-1] if self._context.pages else None
        return {"status": "OK", "pages": len(self._context.pages)}

    def navigate(self, url: str, *, wait_until: str = "domcontentloaded", timeout_ms: int = 30000) -> dict[str, Any]:
        self._check_url(url)
        page = self._ensure()
        response = page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        return {"status": "OK", "url": page.url, "title": page.title(), "http_status": response.status if response else None}

    def simple(self, action: str) -> dict[str, Any]:
        page = self._ensure()
        {"reload": page.reload, "back": page.go_back, "forward": page.go_forward}[action]()
        return {"status": "OK", "url": page.url, "title": page.title()}

    def snapshot(self) -> dict[str, Any]:
        page = self._ensure()
        return {"status": "OK", "url": page.url, "title": page.title(), "text": page.locator("body").inner_text()[:200000]}

    def get_text(self, selector: str = "body") -> dict[str, Any]:
        page = self._ensure()
        return {"status": "OK", "selector": selector, "text": page.locator(selector).inner_text()}

    def find(self, text: str) -> dict[str, Any]:
        page = self._ensure()
        loc = page.get_by_text(text, exact=False)
        return {"status": "OK", "text": text, "count": loc.count()}

    def click(self, selector: str) -> dict[str, Any]:
        self._ensure().locator(selector).click()
        return {"status": "OK", "selector": selector}

    def fill(self, selector: str, value: str) -> dict[str, Any]:
        self._ensure().locator(selector).fill(value)
        return {"status": "OK", "selector": selector}

    def type_text(self, selector: str, value: str, delay_ms: int = 0) -> dict[str, Any]:
        self._ensure().locator(selector).press_sequentially(value, delay=delay_ms)
        return {"status": "OK", "selector": selector}

    def press(self, key: str, selector: str | None = None) -> dict[str, Any]:
        page = self._ensure()
        (page.locator(selector) if selector else page.locator("body")).press(key)
        return {"status": "OK", "key": key}

    def select(self, selector: str, value: str) -> dict[str, Any]:
        values = self._ensure().locator(selector).select_option(value)
        return {"status": "OK", "values": values}

    def wait(self, selector: str | None = None, timeout_ms: int = 1000) -> dict[str, Any]:
        page = self._ensure()
        if selector:
            page.locator(selector).wait_for(timeout=timeout_ms)
        else:
            page.wait_for_timeout(timeout_ms)
        return {"status": "OK"}

    def scroll(self, x: int = 0, y: int = 600) -> dict[str, Any]:
        self._ensure().mouse.wheel(x, y)
        return {"status": "OK", "x": x, "y": y}

    def upload(self, selector: str, path: str) -> dict[str, Any]:
        p = assert_trusted_path(path, must_exist=True)
        self._ensure().locator(selector).set_input_files(str(p))
        return {"status": "OK", "path": str(p)}

    def downloads(self) -> dict[str, Any]:
        rows = []
        for path in sorted(self.downloads_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if path.is_file():
                stat = path.stat()
                rows.append({"name": path.name, "path": str(path), "size": stat.st_size, "modified": stat.st_mtime})
        return {"status": "OK", "downloads": rows[:500]}

    def download_click(self, selector: str, output: str | None = None, timeout_ms: int = 30000) -> dict[str, Any]:
        page = self._ensure()
        with page.expect_download(timeout=timeout_ms) as pending:
            page.locator(selector).click()
        download = pending.value
        if output:
            target = assert_managed_or_trusted_path(output)
        else:
            target = self.downloads_dir / download.suggested_filename
        target.parent.mkdir(parents=True, exist_ok=True)
        download.save_as(str(target))
        return {"status": "OK", "path": str(target), "suggested_filename": download.suggested_filename}

    def screenshot(self, output: str | None = None, *, full_page: bool = False, selector: str | None = None, image_type: str = "png") -> dict[str, Any]:
        page = self._ensure()
        suffix = ".jpg" if image_type.lower() in {"jpg", "jpeg"} else ".png"
        if output:
            p = assert_managed_or_trusted_path(output)
        else:
            import time
            p = install_root() / "screenshots" / f"browser-{int(time.time() * 1000)}{suffix}"
        p.parent.mkdir(parents=True, exist_ok=True)
        kwargs = {"path": str(p), "type": "jpeg" if suffix == ".jpg" else "png"}
        if selector:
            page.locator(selector).screenshot(**kwargs)
        else:
            page.screenshot(full_page=full_page, **kwargs)
        return {"status": "OK", "path": str(p), "url": page.url}

    def console(self) -> dict[str, Any]:
        self._ensure()
        return {"status": "OK", "messages": self._console_messages[-2000:]}

    def network_errors(self) -> dict[str, Any]:
        self._ensure()
        return {"status": "OK", "errors": self._network_errors[-2000:]}

    def video_start(self, *, headless: bool | None = None) -> dict[str, Any]:
        if self._context:
            self.stop()
        return self.start(headless=headless, record_video=True)

    def video_stop(self) -> dict[str, Any]:
        return self.stop()


browser = BrowserManager()
