from __future__ import annotations

import json
import logging
import platform
import shutil
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .config import install_root, load_config
from .safety import sanitize


def logger() -> logging.Logger:
    log = logging.getLogger("codex_local_ops.audit")
    if log.handlers:
        return log
    log.setLevel(getattr(logging, str(load_config().get("logging", {}).get("level", "INFO")).upper(), logging.INFO))
    log.propagate = False
    root = install_root() / "logs"
    root.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(root / "audit.jsonl", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    return log


def emit(tool: str, args: dict[str, Any], result: Any, started: float, *, approval_class: str = "standard") -> None:
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "platform": platform.system().lower(),
        "tool": tool,
        "project": args.get("project") or args.get("path"),
        "target": args.get("target"),
        "host": args.get("host"),
        "sanitized_args": sanitize(args),
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "exit_code": result.get("exit_code") if isinstance(result, dict) else None,
        "status": result.get("status", "OK") if isinstance(result, dict) else "OK",
        "approval_class": approval_class,
    }
    logger().info(json.dumps(record, ensure_ascii=False))


def executable_status(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    return {"available": bool(path), "path": path}
