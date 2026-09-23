from __future__ import annotations

import shutil
from pathlib import Path

from .safety import assert_managed_or_trusted_path, assert_trusted_path


def read_file(path: str, *, max_bytes: int = 1_000_000) -> dict:
    p = assert_trusted_path(path, must_exist=True)
    if not p.is_file():
        raise IsADirectoryError(str(p))
    data = p.read_bytes()
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    return {"path": str(p), "text": data.decode("utf-8", errors="replace"), "bytes": p.stat().st_size, "truncated": truncated}


def write_file(path: str, content: str, *, overwrite: bool = True) -> dict:
    p = assert_trusted_path(path)
    if p.exists() and not overwrite:
        raise FileExistsError(str(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"status": "OK", "path": str(p), "bytes": p.stat().st_size}


def list_dir(path: str) -> dict:
    p = assert_trusted_path(path, must_exist=True)
    if not p.is_dir():
        raise NotADirectoryError(str(p))
    rows = []
    for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        stat = child.lstat()
        rows.append({"name": child.name, "path": str(child), "directory": child.is_dir(), "symlink": child.is_symlink(), "bytes": stat.st_size})
    return {"path": str(p), "items": rows}


def copy(src: str, dst: str) -> dict:
    s = assert_trusted_path(src, must_exist=True)
    d = assert_trusted_path(dst)
    if s.is_dir():
        shutil.copytree(s, d, dirs_exist_ok=True)
    else:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
    return {"status": "OK", "source": str(s), "destination": str(d)}


def move(src: str, dst: str) -> dict:
    s = assert_trusted_path(src, must_exist=True)
    d = assert_trusted_path(dst)
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return {"status": "OK", "source": str(s), "destination": str(d)}


def delete(path: str, *, recursive: bool = False) -> dict:
    p = assert_trusted_path(path, must_exist=True)
    if p.is_dir():
        if not recursive:
            p.rmdir()
        else:
            shutil.rmtree(p)
    else:
        p.unlink()
    return {"status": "OK", "path": str(p)}


def info(path: str) -> dict:
    p = assert_managed_or_trusted_path(path, must_exist=True)
    stat = p.lstat()
    return {"path": str(p), "directory": p.is_dir(), "file": p.is_file(), "symlink": p.is_symlink(), "bytes": stat.st_size, "mtime": stat.st_mtime, "mode": stat.st_mode}
