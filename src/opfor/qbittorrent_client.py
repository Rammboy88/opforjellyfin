"""qBittorrent Web API download backend.

This module provides an alternative to the in-process ``libtorrent``
backend in :mod:`opfor.torrent_client`. It talks to a running qBittorrent
instance via its `Web API`__ so that users who cannot install
``python3-libtorrent`` (for example on systems where they don't have
``sudo``) can still download torrents through ``opfor``.

__ https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API

The only Python dependency used is ``httpx`` which is already required by
the rest of the package.

The shape of :func:`start_torrent` mirrors
:func:`opfor.torrent_client.start_torrent` so both backends are
interchangeable from the orchestrator's point of view.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from . import fsutils
from .config import load_config
from .downloads import save_torrent_download
from .logger import log
from .types import TorrentDownload


# ---------------------------------------------------------------------------
# Minimal bencode decoder (only what's needed to extract the ``info`` dict
# bytes from a .torrent file in order to compute its infohash).
# ---------------------------------------------------------------------------


class BencodeError(ValueError):
    """Raised when a .torrent payload cannot be parsed."""


def _bdecode(data: bytes, idx: int) -> tuple[Any, int]:
    if idx >= len(data):
        raise BencodeError("unexpected end of data")
    ch = data[idx : idx + 1]
    if ch == b"i":
        end = data.index(b"e", idx + 1)
        return int(data[idx + 1 : end]), end + 1
    if ch == b"l":
        idx += 1
        out: list[Any] = []
        while data[idx : idx + 1] != b"e":
            value, idx = _bdecode(data, idx)
            out.append(value)
        return out, idx + 1
    if ch == b"d":
        idx += 1
        out_d: dict[bytes, Any] = {}
        while data[idx : idx + 1] != b"e":
            key, idx = _bdecode(data, idx)
            if not isinstance(key, (bytes, bytearray)):
                raise BencodeError("dict key must be a byte string")
            value, idx = _bdecode(data, idx)
            out_d[bytes(key)] = value
        return out_d, idx + 1
    # byte string: <len>:<bytes>
    if ch.isdigit():
        colon = data.index(b":", idx)
        length = int(data[idx:colon])
        start = colon + 1
        end = start + length
        if end > len(data):
            raise BencodeError("truncated string")
        return data[start:end], end
    raise BencodeError(f"unexpected token {ch!r} at offset {idx}")


def _find_info_dict_bytes(torrent_bytes: bytes) -> bytes:
    """Return the raw bencoded bytes of the top-level ``info`` dictionary.

    The infohash of a torrent is the SHA-1 of the bencoded ``info`` dict
    exactly as it appears in the .torrent file (preserving original byte
    order), so we must locate the slice in the original payload rather than
    re-encode it.
    """
    if not torrent_bytes.startswith(b"d"):
        raise BencodeError("torrent file does not start with a dict")
    idx = 1
    while idx < len(torrent_bytes) and torrent_bytes[idx : idx + 1] != b"e":
        key, idx = _bdecode(torrent_bytes, idx)
        if not isinstance(key, (bytes, bytearray)):
            raise BencodeError("dict key must be a byte string")
        value_start = idx
        _, idx = _bdecode(torrent_bytes, idx)
        if bytes(key) == b"info":
            return torrent_bytes[value_start:idx]
    raise BencodeError("no 'info' key in torrent file")


def compute_infohash(torrent_bytes: bytes) -> str:
    """Return the lowercase hex SHA-1 infohash of ``torrent_bytes``."""
    info_bytes = _find_info_dict_bytes(torrent_bytes)
    return hashlib.sha1(info_bytes).hexdigest()  # noqa: S324 (BitTorrent spec)


# ---------------------------------------------------------------------------
# Web API client
# ---------------------------------------------------------------------------


# qBittorrent torrent states that mean "download finished" (we don't want to
# seed). See https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API
_DONE_STATES = frozenset(
    {
        "uploading",
        "stalledUP",
        "queuedUP",
        "checkingUP",
        "forcedUP",
        "pausedUP",
    }
)


def _check_login(resp: httpx.Response) -> None:
    # qBittorrent returns ``Ok.`` on success and ``Fails.`` on bad creds.
    body = (resp.text or "").strip()
    if resp.status_code != 200 or body.lower() != "ok.":
        raise RuntimeError(
            f"qBittorrent login failed (status={resp.status_code}, body={body!r})"
        )


def _torrent_info(client: httpx.Client, base: str, infohash: str) -> dict | None:
    """Return the qBittorrent ``/torrents/info`` entry for ``infohash`` or ``None``."""
    r = client.get(f"{base}/api/v2/torrents/info", params={"hashes": infohash})
    r.raise_for_status()
    items = r.json()
    if not items:
        return None
    return items[0]


def start_torrent(td: TorrentDownload, cancel_event: threading.Event) -> None:
    """Download ``td`` through a qBittorrent instance.

    Mirrors :func:`opfor.torrent_client.start_torrent`: raises
    ``RuntimeError("cancelled")`` if ``cancel_event`` was set, and any other
    exception on failure.
    """
    cfg = load_config()
    qbt = cfg.qbittorrent
    if not qbt.url:
        raise RuntimeError(
            "qBittorrent backend is selected but no URL is configured. "
            "Run `opfor config qbittorrent --url ...` first."
        )

    base = qbt.url.rstrip("/")
    torrent_url = f"{cfg.source.base_url}/download/{td.torrent_id}.torrent"
    log(False, "qbt: fetching torrent metadata: %s, ID: %d", torrent_url, td.torrent_id)

    try:
        resp = httpx.get(torrent_url, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
        torrent_bytes = resp.content
    except httpx.HTTPError as e:
        log(False, "qbt: HTTP request for metadata failed: %s", e)
        raise

    try:
        infohash = compute_infohash(torrent_bytes)
    except BencodeError as e:
        raise RuntimeError(f"could not parse .torrent file: {e}") from e
    log(False, "qbt: infohash=%s", infohash)

    tmp_dir = fsutils.create_temp_torrent_dir(td.torrent_id)
    save_path = str(tmp_dir.resolve())

    # A dedicated client per worker keeps the session cookie thread-local
    # and avoids races between concurrent downloads.
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        # 1. Login (if credentials supplied; some setups disable auth for
        #    localhost).
        if qbt.username:
            login = client.post(
                f"{base}/api/v2/auth/login",
                data={"username": qbt.username, "password": qbt.password},
                headers={"Referer": base},
            )
            _check_login(login)

        # 2. Add the torrent.
        files = {"torrents": (f"{td.torrent_id}.torrent", torrent_bytes, "application/x-bittorrent")}
        data = {
            "savepath": save_path,
            "category": qbt.category or "opfor",
            "paused": "false",
            "upLimit": "1",  # cap upload at 1 B/s as a "no upload" approximation
            "autoTMM": "false",  # honour our savepath
        }
        add = client.post(f"{base}/api/v2/torrents/add", files=files, data=data)
        if add.status_code != 200 or (add.text or "").strip().lower() not in ("ok.", ""):
            raise RuntimeError(
                f"qBittorrent /torrents/add failed (status={add.status_code}, body={add.text!r})"
            )

        # 3. Wait for qBittorrent to register the torrent. ``add`` returns
        #    immediately and the torrent may not be queryable for a beat.
        info: dict | None = None
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if cancel_event.is_set():
                _try_delete(client, base, infohash, delete_files=True)
                raise RuntimeError("cancelled")
            info = _torrent_info(client, base, infohash)
            if info is not None:
                break
            time.sleep(0.5)
        if info is None:
            raise RuntimeError(
                f"qBittorrent did not register torrent {infohash} within 30s"
            )

        try:
            # 4. Poll progress until done or cancelled.
            while not cancel_event.is_set():
                info = _torrent_info(client, base, infohash) or info
                total_size = int(info.get("size") or info.get("total_size") or 0)
                completed = int(info.get("completed") or info.get("downloaded") or 0)
                state = str(info.get("state") or "")

                if total_size:
                    td.total_size = total_size
                if completed:
                    td.progress = completed
                save_torrent_download(td)

                if state in _DONE_STATES or (
                    total_size > 0 and completed >= total_size
                ):
                    break
                time.sleep(1.0)

            if cancel_event.is_set():
                _try_delete(client, base, infohash, delete_files=True)
                raise RuntimeError("cancelled")

            if td.total_size:
                td.progress = td.total_size
            td.done = True
            td.placement_progress = "⏳ Waiting to place.."
            save_torrent_download(td)
            log(False, "qbt: download complete: %s", td.title)
        finally:
            # Remove the torrent from qBittorrent (without deleting the
            # files) so it doesn't keep seeding. The temp directory is
            # cleaned up by the orchestrator after files are placed.
            _try_delete(client, base, infohash, delete_files=False)


def _try_delete(
    client: httpx.Client, base: str, infohash: str, *, delete_files: bool
) -> None:
    try:
        client.post(
            f"{base}/api/v2/torrents/delete",
            data={
                "hashes": infohash,
                "deleteFiles": "true" if delete_files else "false",
            },
        )
    except httpx.HTTPError as e:  # pragma: no cover - best-effort cleanup
        log(False, "qbt: failed to delete torrent %s: %s", infohash, e)
