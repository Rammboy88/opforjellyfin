"""Active downloads registry (port of `internal/shared/downloads.go`)."""

from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path

from .types import TorrentDownload

_active: dict[int, TorrentDownload] = {}
_lock = threading.RLock()


def save_torrent_download(td: TorrentDownload) -> None:
    with _lock:
        _active[td.torrent_id] = td


def get_active_downloads() -> list[TorrentDownload]:
    with _lock:
        items = list(_active.values())
    items.sort(key=lambda d: d.chapter_range)
    return items


def clear_active_downloads() -> None:
    with _lock:
        _active.clear()
    cleanup_temp_dirs()


def cleanup_temp_dirs() -> None:
    tmp_root = Path(tempfile.gettempdir())
    try:
        for entry in tmp_root.iterdir():
            if entry.name.startswith("opfor-tmp-"):
                shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        pass


def mark_placed(td: TorrentDownload, msg: str) -> None:
    td.placement_progress = msg
    td.placed = True
    save_torrent_download(td)
