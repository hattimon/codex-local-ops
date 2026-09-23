from __future__ import annotations

import platform

from .base import PlatformBackend
from .linux import LinuxBackend
from .macos import MacOSBackend
from .windows import WindowsBackend


def current_backend() -> PlatformBackend:
    system = platform.system().lower()
    if system == "windows":
        return WindowsBackend()
    if system == "linux":
        return LinuxBackend()
    if system == "darwin":
        return MacOSBackend()
    return PlatformBackend()
