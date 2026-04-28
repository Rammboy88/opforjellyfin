"""BitTorrent download client using ``libtorrent``.

Port of `internal/torrent/*.go`.

``libtorrent`` (the Python bindings, package ``python3-libtorrent`` on
Debian/Ubuntu, ``libtorrent`` on PyPI on some platforms) is imported lazily so
the rest of the package remains usable (``opfor list``, ``opfor info`` etc.)
on systems where it is not installed.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import httpx

from . import fsutils, matcher, metadata as md
from .config import load_config
from .downloads import (
    clear_active_downloads,
    get_active_downloads,
    save_torrent_download,
)
from .logger import log
from .types import TorrentDownload, TorrentEntry
from .ui import follow_progress


_lt_module = None


def _import_libtorrent():
    global _lt_module
    if _lt_module is not None:
        return _lt_module
    try:
        import libtorrent as lt  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "libtorrent is required for downloads. Install it with: "
            "`sudo apt install python3-libtorrent` on Debian/Ubuntu."
        ) from e
    _lt_module = lt
    return lt


def start_torrent(td: TorrentDownload, cancel_event: threading.Event) -> None:
    """Download a single torrent. Raises on failure."""
    lt = _import_libtorrent()

    cfg = load_config()
    torrent_url = f"{cfg.source.base_url}/download/{td.torrent_id}.torrent"
    log(False, "Fetching torrent: %s, ID: %d", torrent_url, td.torrent_id)

    # Fetch .torrent metadata
    try:
        resp = httpx.get(torrent_url, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
        torrent_bytes = resp.content
    except httpx.HTTPError as e:
        log(False, "HTTP request for metadata failed: %s", e)
        raise

    tmp_dir = fsutils.create_temp_torrent_dir(td.torrent_id)

    info = lt.torrent_info(lt.bdecode(torrent_bytes))

    session = lt.session(
        {
            "listen_interfaces": "0.0.0.0:0",
            "alert_mask": lt.alert.category_t.error_notification
            | lt.alert.category_t.status_notification,
        }
    )

    handle = session.add_torrent(
        {
            "ti": info,
            "save_path": str(tmp_dir),
            "flags": (
                lt.torrent_flags.upload_mode  # don't upload
                | lt.torrent_flags.auto_managed
            ),
        }
    )

    try:
        td.total_size = info.total_size()
        save_torrent_download(td)
        log(False, "Torrent metadata loaded: %s", td.title)

        # Wait for completion
        while not cancel_event.is_set():
            status = handle.status()
            td.progress = int(status.total_done)
            td.total_size = int(status.total_wanted) or td.total_size
            save_torrent_download(td)
            if status.is_seeding or status.progress >= 1.0:
                break
            time.sleep(1.0)

        if cancel_event.is_set():
            raise RuntimeError("cancelled")

        td.progress = td.total_size
        log(False, "Torrent contains %d files", info.num_files())
        td.done = True
        td.placement_progress = "⏳ Waiting to place.."
        save_torrent_download(td)
        log(False, "Download complete: %s", td.title)
    finally:
        try:
            session.remove_torrent(handle)
        except Exception:  # noqa: BLE001
            pass
        # No explicit close API; let GC handle the session.


# ---------------------------------------------------------------------------
# Concurrent download orchestration (port of HandleDownloadSession)
# ---------------------------------------------------------------------------


MAX_CONCURRENT = 5
DOWNLOAD_TIMEOUT_SEC = 30 * 60


def handle_download_session(entries: list[TorrentEntry], out_dir: str | Path) -> None:
    cancel_event = threading.Event()
    out_dir_p = Path(out_dir)

    metadata_index = md.load_metadata_cache()

    all_tds: list[TorrentDownload] = []
    for entry in entries:
        td = TorrentDownload(
            title=f"[{entry.download_key:4d}] {entry.torrent_name} ({entry.quality})",
            torrent_id=entry.torrent_id,
            full_title=entry.title,
            chapter_range=entry.chapter_range,
        )
        save_torrent_download(td)
        all_tds.append(td)

    # UI thread
    ui_done = threading.Event()
    ui_thread = threading.Thread(
        target=follow_progress, args=(ui_done,), daemon=True
    )
    ui_thread.start()

    def _worker(td: TorrentDownload) -> TorrentDownload:
        try:
            start_torrent(td, cancel_event)
        except RuntimeError as e:
            if str(e) == "cancelled":
                td.placement_progress = "❌ Cancelled"
            else:
                log(True, "Download failed for %s: %s", td.title, e)
                td.placement_progress = "❌ Failed"
            save_torrent_download(td)
            return td
        except Exception as e:  # noqa: BLE001
            log(True, "Download failed for %s: %s", td.title, e)
            td.placement_progress = "❌ Failed"
            save_torrent_download(td)
            return td

        # Place files immediately after download completes
        tmp_dir = Path(tempfile.gettempdir()) / f"opfor-tmp-{td.torrent_id}"
        try:
            matcher.process_torrent_files(tmp_dir, out_dir_p, td, metadata_index)
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except OSError as e:
                log(False, "Failed to remove temp dir %s: %s", tmp_dir, e)
        return td

    placed: list[TorrentDownload] = []
    cancelled = False
    try:
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as pool:
            futures = [pool.submit(_worker, td) for td in all_tds]
            for fut in as_completed(futures):
                placed.append(fut.result())
    except KeyboardInterrupt:
        log(True, "\n🛑 Received interrupt signal, cancelling downloads...")
        cancel_event.set()
        cancelled = True

    # Stop UI
    ui_done.set()
    ui_thread.join(timeout=2.0)

    for td in placed:
        if td.placement_full:
            print(f"🎞️  {td.title}")
            for line in td.placement_full:
                print(f"   → {line}")

    clear_active_downloads()

    if cancelled:
        log(True, "\n❌ Downloads cancelled by user.")
    else:
        log(True, "\n✅ All downloads finished and placed.")
