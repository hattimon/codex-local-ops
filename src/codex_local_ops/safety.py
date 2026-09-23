from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .config import load_config


SECRET_PATTERNS = [
    re.compile(r"(?i)(password|token|secret|authorization|cookie|session|api[_-]?key|github[_-]?pat|obs[_-]?password)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]


def redact_text(value: str) -> str:
    out = value
    for pattern in SECRET_PATTERNS:
        if "PRIVATE KEY" in pattern.pattern:
            out = pattern.sub("[REDACTED_PRIVATE_KEY]", out)
        elif "bearer" in pattern.pattern.lower():
            out = pattern.sub("Bearer [REDACTED]", out)
        elif "github_pat_" in pattern.pattern:
            out = pattern.sub("[REDACTED_GITHUB_TOKEN]", out)
        else:
            out = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED]", out)
    return out


def sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            normalized = name.lower().replace("-", "_")
            if normalized == "session_id":
                out[name] = sanitize(item)
            elif any(marker in normalized for marker in ("password", "secret", "token", "authorization", "cookie", "session", "api_key", "apikey", "private_key")):
                out[name] = "[REDACTED]"
            else:
                out[name] = sanitize(item)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    return value


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def trusted_roots() -> list[Path]:
    cfg = load_config()
    roots = []
    for item in cfg.get("projects", {}).get("trusted_roots", []):
        try:
            roots.append(_resolved(Path(item)))
        except OSError:
            continue
    return roots


def assert_trusted_path(path: str | Path, *, must_exist: bool = False) -> Path:
    candidate = Path(path).expanduser()
    if must_exist and not candidate.exists():
        raise FileNotFoundError(str(candidate))
    resolved = _resolved(candidate)
    if os.name == "nt" and str(resolved).startswith("\\\\"):
        roots = trusted_roots()
        if not any(str(root).startswith("\\\\") and resolved.is_relative_to(root) for root in roots):
            raise PermissionError("UNC/network path is not explicitly trusted")
    for root in trusted_roots():
        try:
            resolved.relative_to(root)
            current = resolved
            while current != root and current.exists():
                if current.is_symlink():
                    target = current.resolve(strict=True)
                    target.relative_to(root)
                current = current.parent
            return resolved
        except (ValueError, OSError):
            continue
    raise PermissionError(f"Path is outside trusted roots: {resolved}")


def assert_managed_or_trusted_path(path: str | Path, *, must_exist: bool = False) -> Path:
    from .config import install_root

    candidate = Path(path).expanduser()
    if must_exist and not candidate.exists():
        raise FileNotFoundError(str(candidate))
    resolved = _resolved(candidate)
    managed = _resolved(install_root())
    try:
        resolved.relative_to(managed)
        return resolved
    except ValueError:
        return assert_trusted_path(resolved, must_exist=must_exist)


def computer_mode() -> str:
    return str(load_config().get("computer_control", {}).get("mode", "SAFE")).upper()


def expert_mode() -> bool:
    return bool(load_config().get("permissions", {}).get("expert_mode", False))


def permission_profile() -> str:
    """The Local Ops execution permission, independent of desktop-control mode."""
    return str(load_config().get("permissions", {}).get("local_profile", "SAFE")).upper()


def require_raw_execution() -> None:
    """Raw command tools require a deliberately elevated Local Ops profile."""
    if expert_mode() or permission_profile() in {"DEVELOPER", "FULL"}:
        return
    raise PermissionError("Raw command execution requires permissions.local_profile DEVELOPER/FULL or expert_mode")


def require_mode(*allowed: str) -> None:
    mode = computer_mode()
    if mode not in {x.upper() for x in allowed}:
        raise PermissionError(f"Computer control mode {mode} does not allow this operation")
