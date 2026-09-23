from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import psutil

from .config import install_root
from .safety import redact_text


TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "FINISHED_EXIT_UNKNOWN"}
_STATE_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    root = install_root() / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_dir(session_id: str) -> Path:
    if not session_id.startswith("job_") or any(ch not in "0123456789abcdef" for ch in session_id[4:]):
        raise ValueError("invalid session_id")
    path = _root() / session_id
    if not path.is_dir():
        raise FileNotFoundError(f"Unknown job session: {session_id}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    result = dict(state)
    result.pop("_cleanup_paths", None)
    if "argv" in result:
        result["argv"] = [redact_text(str(x)) for x in result["argv"]]
    return result


def _cleanup_paths(state: dict[str, Any]) -> bool:
    ok = True
    for raw in state.get("_cleanup_paths", []) or []:
        path = Path(str(raw))
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            ok = False
    return ok


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        proc = psutil.Process(int(pid))
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, ValueError, TypeError):
        return False


def _watch(proc: subprocess.Popen[Any], state_path: Path, started: float) -> None:
    try:
        exit_code = proc.wait()
        with _STATE_LOCK:
            state = _read_json(state_path)
            cleanup_complete = _cleanup_paths(state)
            if state.get("status") != "CANCELLED":
                state.update(
                    {
                        "status": "COMPLETED" if exit_code == 0 else "FAILED",
                        "exit_code": exit_code,
                        "finished_at": _now(),
                        "duration_ms": round((time.monotonic() - started) * 1000, 1),
                        "cleanup_complete": cleanup_complete,
                    }
                )
                _write_json(state_path, state)
    except Exception as exc:
        try:
            with _STATE_LOCK:
                state = _read_json(state_path)
                cleanup_complete = _cleanup_paths(state)
                if state.get("status") != "CANCELLED":
                    state.update(
                        {
                            "status": "FAILED",
                            "reason": redact_text(str(exc)),
                            "finished_at": _now(),
                            "cleanup_complete": cleanup_complete,
                        }
                    )
                    _write_json(state_path, state)
        except Exception:
            pass


def start(
    argv: Iterable[str],
    *,
    cwd: Path | None = None,
    label: str | None = None,
    cleanup_paths: Iterable[Path | str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    args = [str(x) for x in argv]
    if not args:
        raise ValueError("argv must not be empty")

    session_id = f"job_{uuid.uuid4().hex}"
    session_dir = _root() / session_id
    session_dir.mkdir(parents=False, exist_ok=False)
    created_at = _now()
    state = {
        "status": "STARTING",
        "session_id": session_id,
        "label": label,
        "argv": [redact_text(x) for x in args],
        "cwd": str(cwd) if cwd else None,
        "created_at": created_at,
        "child_pid": None,
        "exit_code": None,
        "cleanup_complete": False,
        "_cleanup_paths": [str(Path(x)) for x in (cleanup_paths or [])],
    }
    with _STATE_LOCK:
        _write_json(session_dir / "state.json", state)

    stdout = (session_dir / "stdout.log").open("ab", buffering=0)
    stderr = (session_dir / "stderr.log").open("ab", buffering=0)
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
            if os.name == "nt"
            else 0,
            start_new_session=os.name != "nt",
        )
    finally:
        stdout.close()
        stderr.close()

    state.update({"status": "RUNNING", "child_pid": proc.pid, "started_at": _now()})
    with _STATE_LOCK:
        _write_json(session_dir / "state.json", state)
    threading.Thread(
        target=_watch,
        args=(proc, session_dir / "state.json", time.monotonic()),
        name=f"codexLocalOps-{session_id}",
        daemon=True,
    ).start()
    return {
        "status": "STARTED",
        "session_id": session_id,
        "child_pid": proc.pid,
        "created_at": created_at,
    }


def status(session_id: str) -> dict[str, Any]:
    state_path = _session_dir(session_id) / "state.json"
    with _STATE_LOCK:
        state = _read_json(state_path)
    if state.get("status") not in TERMINAL_STATES and not _pid_alive(state.get("child_pid")):
        time.sleep(0.03)
        with _STATE_LOCK:
            state = _read_json(state_path)
            if state.get("status") not in TERMINAL_STATES and not _pid_alive(state.get("child_pid")):
                state.update(
                    {
                        "status": "FINISHED_EXIT_UNKNOWN",
                        "exit_code": None,
                        "finished_at": _now(),
                        "reason": "Process ended after the originating MCP process exited; exit code unavailable.",
                        "cleanup_complete": _cleanup_paths(state),
                    }
                )
                _write_json(state_path, state)
    elif state.get("status") in TERMINAL_STATES:
        with _STATE_LOCK:
            state = _read_json(state_path)
            state["cleanup_complete"] = not _pid_alive(state.get("child_pid")) and _cleanup_paths(state)
            _write_json(state_path, state)
    return _public_state(state)


def _read_tail(path: Path, max_bytes: int) -> tuple[str, bool, int]:
    if not path.exists():
        return "", False, 0
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
        data = fh.read(max_bytes)
    return redact_text(data.decode("utf-8", errors="replace")), size > max_bytes, size


def output(session_id: str, *, max_bytes: int = 200_000) -> dict[str, Any]:
    session_dir = _session_dir(session_id)
    limit = max(1, min(int(max_bytes), 2_000_000))
    out, out_truncated, out_size = _read_tail(session_dir / "stdout.log", limit)
    err, err_truncated, err_size = _read_tail(session_dir / "stderr.log", limit)
    return {
        "status": "OK",
        "session_id": session_id,
        "job_status": status(session_id).get("status"),
        "stdout": out,
        "stderr": err,
        "stdout_bytes": out_size,
        "stderr_bytes": err_size,
        "stdout_truncated": out_truncated,
        "stderr_truncated": err_truncated,
    }


def _terminate_tree(pid: int | None) -> None:
    if not pid:
        return
    try:
        proc = psutil.Process(int(pid))
    except (psutil.Error, ValueError, TypeError):
        return
    targets = proc.children(recursive=True)
    targets.append(proc)
    for item in reversed(targets):
        try:
            item.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(targets, timeout=2)
    for item in alive:
        try:
            item.kill()
        except psutil.Error:
            pass


def cancel(session_id: str) -> dict[str, Any]:
    state_path = _session_dir(session_id) / "state.json"
    with _STATE_LOCK:
        state = _read_json(state_path)
        if state.get("status") in TERMINAL_STATES:
            return _public_state(state)
        _terminate_tree(state.get("child_pid"))
        state.update(
            {
                "status": "CANCELLED",
                "cancelled_at": _now(),
                "finished_at": _now(),
                "cleanup_complete": _cleanup_paths(state),
            }
        )
        _write_json(state_path, state)
        return _public_state(state)


def list_jobs(*, limit: int = 50) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in sorted(_root().glob("job_*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if len(rows) >= max(1, min(int(limit), 500)):
            break
        try:
            rows.append(status(item.name))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return {"status": "OK", "jobs": rows, "count": len(rows)}
