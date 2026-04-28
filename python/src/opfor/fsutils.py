"""Filesystem helpers (port of `internal/shared/fsutils.go`).

A single ``threading.Lock`` mirrors the Go ``dirMutex`` and serialises
directory creation / file moves to fix the concurrency bug noted in the
README of the Go version.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path

from .logger import log

_dir_lock = threading.Lock()


def create_temp_torrent_dir(torrent_id: int) -> Path:
    """Create a stable temp directory ``opfor-tmp-<id>`` under the system tempdir."""
    tmp = Path(tempfile.gettempdir()) / f"opfor-tmp-{torrent_id}"
    with _dir_lock:
        if tmp.is_dir():
            log(False, "Temp dir already exists: %s", tmp)
            return tmp
        tmp.mkdir(parents=True, exist_ok=True)
        log(False, "Created temp dir: %s", tmp)
    return tmp


def safe_move_file(src: str | Path, dst: str | Path) -> None:
    """Move ``src`` to ``dst``, creating parent dirs. Raises on failure."""
    src_p = Path(src)
    dst_p = Path(dst)
    with _dir_lock:
        log(False, "sfm: starting move from %s to %s", src_p, dst_p)
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_p, dst_p)
        src_p.unlink()
        log(False, "sfm: source file removed")


def copy_file(src: str | Path, dst: str | Path) -> None:
    with _dir_lock:
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def create_directory(path: str | Path) -> None:
    with _dir_lock:
        Path(path).mkdir(parents=True, exist_ok=True)


def file_exists(path: str | Path) -> bool:
    p = Path(path)
    return p.exists() and p.is_file()


def _walk_and_copy(src: Path, dst: Path, only_if_changed: bool) -> None:
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if only_if_changed and target.exists():
            try:
                if target.read_bytes() == p.read_bytes():
                    continue
            except OSError:
                pass
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)


def copy_dir(src: str | Path, dst: str | Path) -> None:
    """Recursive copy (overwrites)."""
    with _dir_lock:
        _walk_and_copy(Path(src), Path(dst), only_if_changed=False)


def sync_dir(src: str | Path, dst: str | Path) -> None:
    """Recursive copy of new/changed files only."""
    with _dir_lock:
        _walk_and_copy(Path(src), Path(dst), only_if_changed=True)
